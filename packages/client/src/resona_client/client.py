"""
ResonaClient — HTTP client for the Resona hub.

Configuration (in priority order):
  1. Explicit base_url / api_key arguments
  2. RESONA_API_URL / RESONA_API_KEY environment variables
  3. WS_API_URL / WS_API_KEY environment variables (fallback for migration)
  4. ~/.resona/config.json (via ResonaClient.from_config())

Every request authenticates with ``Authorization: Bearer <key>``. The hub derives
the organization from the credential; the client never sends a tenant identifier.
"""
import logging
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, NoReturn, Optional, Union

import httpx
from pydantic import BaseModel, ValidationError
from resona_job_core import JobResult
from resona_job_core.options import AudioOptions, DocumentOptions

log = logging.getLogger(__name__)

JobKind = Literal["audio", "document"]
JobPurpose = Literal["production", "evaluation"]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_POST_JOB_ATTEMPTS = 3
_RETRYABLE_STATUSES = frozenset({502, 503, 504})


_OPTIONS_MODEL: dict[str, type[BaseModel]] = {
    "audio": AudioOptions,
    "document": DocumentOptions,
}


class ResonaAPIError(Exception):
    """A non-2xx hub response, carrying the hub's machine-readable error code."""

    def __init__(self, status: int, code: Optional[str], message: str):
        self.status = status
        self.code = code
        self.message = message
        label = code or "error"
        super().__init__(f"{status} {label}: {message}" if message else f"{status} {label}")


class PolicyDeniedError(ResonaAPIError):
    """403 policy_denied: the hub refused the job or stage under current policy."""


class JobNotReady(ResonaAPIError):
    """409 on result fetch: the job has not completed yet."""


class ResultExpired(ResonaAPIError):
    """410 on result fetch: removed by retention (``result_expired``) or lost in a hub restore (``content_unavailable``)."""


class RevisionConflict(ResonaAPIError):
    """409 on a revisioned write: settings If-Match or expected baseline revision is stale."""


class SyncWaitTimeout(ResonaAPIError):
    """504 from the synchronous adapter: the job was queued and keeps running."""

    def __init__(self, status: int, code: Optional[str], message: str, *, job_id: int):
        super().__init__(status, code, message)
        self.job_id = job_id


class JobTimeout(TimeoutError):
    """Client-side wait deadline passed; the job itself keeps running on the hub."""

    def __init__(self, job_id: int):
        self.job_id = job_id
        super().__init__(f"Job {job_id} did not reach a terminal state before the timeout")


@dataclass(frozen=True)
class Upload:
    upload_id: str
    sha256: str
    size: int


@dataclass(frozen=True)
class UploadLink:
    """A single-use upload link: send multipart ``file`` to ``upload_url`` with
    ``token_header: token`` before ``expires_at`` (ISO 8601 UTC)."""

    upload_url: str
    token_header: str
    token: str
    expires_at: str
    max_bytes: int


@dataclass(frozen=True)
class JobRef:
    job_id: int
    status: str


@dataclass(frozen=True)
class JobView:
    job_id: int
    kind: str
    status: str
    private: bool
    execution_profile: Optional[str]
    queue_reason: Optional[str]
    candidate: Optional[dict]
    attempt: int
    error: Optional[dict]
    result_available: bool
    raw: Mapping[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobView":
        return cls(
            job_id=int(data["job_id"]),
            kind=str(data["kind"]),
            status=str(data["status"]),
            private=bool(data["private"]),
            execution_profile=data.get("execution_profile"),
            queue_reason=data.get("queue_reason"),
            candidate=data.get("candidate"),
            attempt=int(data.get("attempt") or 0),
            error=data.get("error"),
            result_available=bool(data.get("result_available", False)),
            raw=dict(data),
        )


@dataclass(frozen=True)
class JobPage:
    items: list[JobView]
    next_cursor: Optional[str]


@dataclass(frozen=True)
class MarkdownChunk:
    """A character range of a result's Markdown; ``next_offset`` is None at the end."""

    text: str
    offset: int
    next_offset: Optional[int]
    total_chars: int
    kind: Optional[str] = None
    language: Optional[str] = None
    page_count: int = 0
    warnings: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarkdownChunk":
        return cls(
            text=data["text"],
            offset=data["offset"],
            next_offset=data.get("next_offset"),
            total_chars=data["total_chars"],
            kind=data.get("kind"),
            language=data.get("language"),
            page_count=int(data.get("page_count") or 0),
            warnings=tuple(data.get("warnings") or ()),
            provenance=dict(data.get("provenance") or {}),
        )


@dataclass(frozen=True)
class SettingsDoc:
    """One revision of the organization's settings; ``etag`` feeds ``put_settings``."""

    organization_id: str
    revision: int
    etag: str
    settings: dict


def _parse_error_body(resp: httpx.Response) -> tuple[Optional[str], str]:
    """Extract (code, message) from ``{error, message}`` or FastAPI ``{detail: ...}``."""
    try:
        body = resp.json()
    except ValueError:
        return None, resp.reason_phrase or ""
    if not isinstance(body, dict):
        return None, ""
    detail = body.get("detail", body)
    if isinstance(detail, str):
        return None, detail
    if not isinstance(detail, dict):
        return None, ""
    code = detail.get("error")
    message = detail.get("message")
    return (
        code if isinstance(code, str) else None,
        message if isinstance(message, str) else "",
    )


def _raise_for(
    resp: httpx.Response,
    expected: Optional[Mapping[int, type[ResonaAPIError]]] = None,
) -> NoReturn:
    code, message = _parse_error_body(resp)
    if resp.status_code == 403 and code == "policy_denied":
        raise PolicyDeniedError(resp.status_code, code, message)
    cls = (expected or {}).get(resp.status_code, ResonaAPIError)
    raise cls(resp.status_code, code, message)


def _checked_options(
    kind: str,
    options: Union[AudioOptions, DocumentOptions, Mapping[str, Any], None],
) -> Optional[dict]:
    model = _OPTIONS_MODEL.get(kind)
    if model is None:
        raise ValueError(f"unknown job kind {kind!r}; expected 'audio' or 'document'")
    if options is None:
        return None
    if isinstance(options, BaseModel):
        if not isinstance(options, model):
            raise ValueError(f"{type(options).__name__} cannot be used for a {kind} job")
        checked = options
    else:
        try:
            checked = model.model_validate(dict(options))
        except ValidationError as exc:
            raise ValueError(f"invalid {kind} options: {exc}") from exc
    return checked.model_dump(mode="json", exclude_none=True)


class ResonaClient:
    """HTTP client for the Resona hub."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 3600.0,
    ):
        self.base_url = (
            base_url
            or os.getenv("RESONA_API_URL")
            or os.getenv("WS_API_URL", "http://127.0.0.1:7000")
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.getenv("RESONA_API_KEY")
            or os.getenv("WS_API_KEY", "")
        )
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )

    @classmethod
    def from_config(
        cls,
        auto_start: bool = True,
        timeout: float = 3600.0,
        name: Optional[str] = None,
    ) -> "ResonaClient":
        """
        Create a client by resolving the engine to use:

        1. RESONA_API_URL env var (if set) — used directly, no config lookup.
        2. WS_API_URL env var (if set) — used directly, no config lookup (fallback).
        3. With ``name``: only that entry in ~/.resona/config.json, never another.
           Otherwise the first reachable entry.
        4. If none reachable and an engine has compose_dir set, start it via
           docker compose up -d and wait for it to become healthy.

        Raises RuntimeError if no engine could be resolved.
        """
        env_url = os.getenv("RESONA_API_URL") or os.getenv("WS_API_URL")
        if env_url:
            return cls(base_url=env_url, timeout=timeout)

        from .config import resolve_engine
        entry = resolve_engine(auto_start=auto_start, name=name)
        if entry:
            return cls(base_url=entry.api_url, api_key=entry.api_key, timeout=timeout)

        raise RuntimeError(
            "No reachable resona engine found.\n"
            "Add one with:  resona engines add <name> <url>"
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        expected: Optional[Mapping[int, type[ResonaAPIError]]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        resp = self._client.request(method, f"{self.base_url}{path}", **kwargs)
        if resp.is_error:
            _raise_for(resp, expected)
        return resp

    # ── Upload & job API ──────────────────────────────────────────────

    def upload(self, filepath: Path | str) -> Upload:
        """Upload a file for one later job. POST /v1/uploads"""
        path = Path(filepath)
        with path.open("rb") as fh:
            return self._post_upload(path.name, fh)

    def upload_bytes(self, filename: str, data: bytes) -> Upload:
        """Upload in-memory content for one later job. POST /v1/uploads

        ``filename`` only supplies a content-type hint; the hub stores generated names
        and identifies the media family from the content itself.
        """
        return self._post_upload(filename, data)

    def _post_upload(self, filename: str, content: Any) -> Upload:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        resp = self._send(
            "POST", "/v1/uploads",
            files={"file": (filename, content, content_type)},
        )
        body = resp.json()
        return Upload(
            upload_id=str(body["upload_id"]),
            sha256=str(body["sha256"]),
            size=int(body["size"]),
        )

    def create_upload_link(self) -> UploadLink:
        """Mint a single-use, ten-minute upload link. POST /v1/upload-links

        Whoever holds the token can upload one file into this organization without
        the client credential.
        """
        body = self._send("POST", "/v1/upload-links").json()
        return UploadLink(
            upload_url=str(body["upload_url"]),
            token_header=str(body["token_header"]),
            token=str(body["token"]),
            expires_at=str(body["expires_at"]),
            max_bytes=int(body["max_bytes"]),
        )

    def submit_job(
        self,
        filepath: Path | str,
        *,
        kind: JobKind,
        execution_profile: Optional[str] = None,
        language: Optional[str] = None,
        engine: Optional[str] = None,
        model: Optional[str] = None,
        profile: Optional[str] = None,
        options: Union[AudioOptions, DocumentOptions, Mapping[str, Any], None] = None,
        private: bool = True,
        idempotency_key: Optional[str] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        purpose: JobPurpose = "production",
        release_id: Optional[str] = None,
    ) -> JobRef:
        """Upload a file and create a job for it. POST /v1/uploads, then POST /v1/jobs.

        The Idempotency-Key is generated once per call (or taken from the argument)
        and reused for every retry of the job POST, so a lost response cannot create
        a duplicate job. The upload is not repeated; the retry reuses its upload_id.
        """
        _checked_options(kind, options)
        upload = self.upload(filepath)
        return self.submit_upload(
            upload.upload_id,
            kind=kind,
            execution_profile=execution_profile,
            language=language,
            engine=engine,
            model=model,
            profile=profile,
            options=options,
            private=private,
            idempotency_key=idempotency_key,
            provenance=provenance,
            purpose=purpose,
            release_id=release_id,
        )

    def submit_upload(
        self,
        upload_id: str,
        *,
        kind: JobKind,
        execution_profile: Optional[str] = None,
        language: Optional[str] = None,
        engine: Optional[str] = None,
        model: Optional[str] = None,
        profile: Optional[str] = None,
        options: Union[AudioOptions, DocumentOptions, Mapping[str, Any], None] = None,
        private: bool = True,
        idempotency_key: Optional[str] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        purpose: JobPurpose = "production",
        release_id: Optional[str] = None,
    ) -> JobRef:
        """Create a job for an existing upload. POST /v1/jobs

        The Idempotency-Key (generated unless given) is reused for every retry of
        the POST, so a lost response cannot create a duplicate job.
        ``purpose='evaluation'`` needs an org-admin credential and the ``release_id``
        of a release staged on the hub (``resona-admin release stage``).
        """
        checked = _checked_options(kind, options)
        key = idempotency_key or str(uuid.uuid4())
        body: dict[str, Any] = {
            "kind": kind,
            "upload_id": upload_id,
            "private": private,
        }
        optional: dict[str, Any] = {
            "execution_profile": execution_profile,
            "language": language,
            "engine": engine,
            "model": model,
            "profile": profile,
            "options": checked,
            "provenance": dict(provenance) if provenance is not None else None,
            "purpose": purpose if purpose != "production" else None,
            "release_id": release_id,
        }
        body.update({k: v for k, v in optional.items() if v is not None})

        resp = self._post_job(body, key)
        data = resp.json()
        return JobRef(job_id=int(data["job_id"]), status=str(data["status"]))

    def _post_job(self, body: dict[str, Any], key: str) -> httpx.Response:
        headers = {"Idempotency-Key": key}
        last_error: Optional[Exception] = None
        for attempt in range(_POST_JOB_ATTEMPTS):
            if attempt:
                time.sleep(min(0.5 * 2 ** attempt, 4.0))
            try:
                resp = self._client.post(
                    f"{self.base_url}/v1/jobs", json=body, headers=headers,
                )
            except httpx.TransportError as exc:
                last_error = exc
                continue
            if resp.status_code in _RETRYABLE_STATUSES and attempt < _POST_JOB_ATTEMPTS - 1:
                last_error = ResonaAPIError(*_status_and_error(resp))
                continue
            if resp.is_error:
                _raise_for(resp)
            return resp
        assert last_error is not None
        raise last_error

    def get_job(self, job_id: int) -> JobView:
        """Get one job's state. GET /v1/jobs/{id}"""
        return JobView.from_dict(self._send("GET", f"/v1/jobs/{job_id}").json())

    def list_jobs(self, *, cursor: Optional[str] = None, limit: int = 50) -> JobPage:
        """List the organization's jobs, newest first. GET /v1/jobs"""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = self._send("GET", "/v1/jobs", params=params).json()
        return JobPage(
            items=[JobView.from_dict(item) for item in body["items"]],
            next_cursor=body.get("next_cursor"),
        )

    def wait_for_job(
        self,
        job_id: int,
        *,
        timeout: Optional[float] = None,
        poll: float = 1.0,
    ) -> JobView:
        """Poll until the job is completed, failed or cancelled.

        Raises JobTimeout when the client deadline passes. The job keeps running on
        the hub; the caller decides what to do. Never falls back to local processing.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.terminal:
                return job
            if deadline is None:
                time.sleep(poll)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobTimeout(job_id)
            time.sleep(min(poll, remaining))

    def get_result(self, job_id: int) -> JobResult:
        """Fetch a completed job's result. GET /v1/jobs/{id}/result

        Raises JobNotReady (409) before completion and ResultExpired (410) after retention.
        """
        resp = self._send(
            "GET", f"/v1/jobs/{job_id}/result",
            expected={409: JobNotReady, 410: ResultExpired},
        )
        return JobResult.model_validate_json(resp.content)

    def get_result_markdown(self, job_id: int, *, offset: int = 0, limit: int = 32768) -> MarkdownChunk:
        """One character range of a completed job's Markdown.
        GET /v1/jobs/{id}/result/markdown

        ``limit`` is at most 32768 characters. Raises JobNotReady (409) before
        completion and ResultExpired (410) after retention.
        """
        resp = self._send(
            "GET", f"/v1/jobs/{job_id}/result/markdown",
            params={"offset": offset, "limit": limit},
            expected={409: JobNotReady, 410: ResultExpired},
        )
        return MarkdownChunk.from_dict(resp.json())

    def cancel_job(self, job_id: int) -> JobView:
        """Request cancellation; the lease is fenced. POST /v1/jobs/{id}/cancel"""
        return JobView.from_dict(self._send("POST", f"/v1/jobs/{job_id}/cancel").json())

    def delete_job(self, job_id: int) -> None:
        """Delete a job's input and result now. DELETE /v1/jobs/{id}"""
        self._send("DELETE", f"/v1/jobs/{job_id}")

    # ── Organization settings ─────────────────────────────────────────

    def get_settings(self) -> SettingsDoc:
        """Return the organization's current settings revision. GET /v1/settings"""
        return _settings_from(self._send("GET", "/v1/settings"))

    def put_settings(
        self,
        settings: Mapping[str, Any],
        *,
        if_match: str,
    ) -> SettingsDoc:
        """Replace organization settings; ``if_match`` is the etag from get_settings.

        Raises RevisionConflict when the revision is stale. PUT /v1/settings
        """
        resp = self._send(
            "PUT", "/v1/settings",
            json=dict(settings),
            headers={"If-Match": if_match},
            expected={409: RevisionConflict},
        )
        return _settings_from(resp)

    # ── Releases, evaluations and baselines ───────────────────────────

    def get_version(self) -> dict:
        """Hub release and worker protocol. GET /v1/version (no auth needed)"""
        return self._send("GET", "/v1/version").json()

    def create_evaluation(self, evaluation: Mapping[str, Any]) -> dict:
        """Record an evaluation summary (metrics only). POST /v1/evaluations (org_admin)"""
        return self._send("POST", "/v1/evaluations", json=dict(evaluation)).json()

    def get_evaluation(self, evaluation_id: int) -> dict:
        """One recorded evaluation. GET /v1/evaluations/{id} (org_admin)"""
        return self._send("GET", f"/v1/evaluations/{evaluation_id}").json()

    def get_baseline(self, *, execution_profile: str, platform: str) -> Optional[dict]:
        """Current baseline for the profile/platform, or None (hub 404 ``baseline_required``).

        GET /v1/baselines (org_admin)
        """
        resp = self._client.get(
            f"{self.base_url}/v1/baselines",
            params={"execution_profile": execution_profile, "platform": platform},
        )
        if resp.status_code == 404 and _parse_error_body(resp)[0] == "baseline_required":
            return None
        if resp.is_error:
            _raise_for(resp)
        return resp.json()

    def promote_evaluation(
        self, evaluation_id: int, *, expected_baseline_revision: Optional[int], rebaseline: bool = False,
    ) -> dict:
        """Make a recorded, passing evaluation the new baseline. POST /v1/evaluations/{id}/promote

        Raises RevisionConflict on 409: ``baseline_revision_mismatch`` when the baseline
        moved since ``expected_baseline_revision`` was read, ``baseline_corpus_mismatch``
        when the evaluation used another corpus and ``rebaseline`` was not requested.
        """
        return self._send(
            "POST", f"/v1/evaluations/{evaluation_id}/promote",
            json={"expected_baseline_revision": expected_baseline_revision, "rebaseline": rebaseline},
            expected={409: RevisionConflict},
        ).json()

    # ── Testbench routes ──────────────────────────────────────────────

    def testbench_status(self) -> dict:
        """Return whether the testbench is enabled and its counters. GET /v1/testbench/status (client|org_admin)"""
        return self._send("GET", "/v1/testbench/status").json()

    def testbench_contribute(
        self,
        path: "Path | str",
        *,
        language: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict:
        """Contribute a recording or scan; the hub persists it and queues a draft run. POST /v1/testbench/items (client|org_admin)

        Raises ResonaAPIError with code ``testbench_disabled`` (403) while the
        organization has not enabled the testbench.
        """
        path = Path(path)
        data = {k: v for k, v in {"language": language, "label": label}.items() if v}
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            return self._send(
                "POST", "/v1/testbench/items",
                files={"file": (path.name, fh, content_type)},
                data=data,
            ).json()

    def testbench_list_items(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """List testbench items, newest first. GET /v1/testbench/items (org_admin)"""
        params = {
            k: v for k, v in
            {"kind": kind, "status": status, "limit": limit, "cursor": cursor}.items()
            if v is not None
        }
        return self._send("GET", "/v1/testbench/items", params=params).json()

    def testbench_get_item(self, item_id: int) -> dict:
        """Get one item with its reference and runs. GET /v1/testbench/items/{id} (org_admin)"""
        return self._send("GET", f"/v1/testbench/items/{item_id}").json()

    def testbench_set_reference(self, item_id: int, text: str) -> dict:
        """Set the item's reference text and re-score its runs. PUT /v1/testbench/items/{id}/reference (org_admin)"""
        return self._send(
            "PUT", f"/v1/testbench/items/{item_id}/reference", json={"text": text},
        ).json()

    def testbench_delete_item(self, item_id: int) -> None:
        """Delete an item, its files and its runs. DELETE /v1/testbench/items/{id} (org_admin)"""
        self._send("DELETE", f"/v1/testbench/items/{item_id}")

    def testbench_candidates(self) -> dict:
        """List candidates approved workers have tested. GET /v1/testbench/candidates (org_admin)"""
        return self._send("GET", "/v1/testbench/candidates").json()

    def testbench_run(self, item_id: int, candidates: list[Mapping[str, Any]]) -> dict:
        """Run an item through the given candidates. POST /v1/testbench/items/{id}/runs (org_admin)"""
        return self._send(
            "POST", f"/v1/testbench/items/{item_id}/runs",
            json={"candidates": [dict(c) for c in candidates]},
        ).json()

    def testbench_get_run(self, item_id: int, run_id: int) -> dict:
        """Get one run with text and word alignment. GET /v1/testbench/items/{id}/runs/{run_id} (org_admin)"""
        return self._send("GET", f"/v1/testbench/items/{item_id}/runs/{run_id}").json()

    def testbench_leaderboard(self) -> dict:
        """Rank candidates over corrected items (draft runs excluded). GET /v1/testbench/leaderboard (org_admin)"""
        return self._send("GET", "/v1/testbench/leaderboard").json()

    # ── v1 Audio & Engine routes ──────────────────────────────────────

    def list_engines(self) -> dict:
        """List every engine the gateway exposes, with capabilities and status. GET /v1/engines"""
        return self._send("GET", "/v1/engines").json()

    def list_listen_engines(self) -> dict:
        """List the engines this organization can stream live via ``WS /v1/listen``. GET /v1/listen/engines

        Returns:
            Dict with keys ``engines`` (``name``, ``kind``, ``private``, ``models``) and ``default``.
        """
        return self._send("GET", "/v1/listen/engines").json()

    def create_transcription(
        self,
        audio_path: "Path | str",
        *,
        model: str = "whisper-1",
        language: Optional[str] = None,
        response_format: str = "json",
        engine: Optional[str] = None,
        execution_profile: Optional[str] = None,
        private: bool = True,
        profile: Optional[str] = None,
    ) -> dict:
        """Transcribe audio through the OpenAI-compatible adapter. POST /v1/audio/transcriptions

        The hub queues a normal audio job and waits a bounded time for a worker.
        ``model="whisper-1"`` means the execution profile's default; any other value
        is an override that must be an approved candidate.

        Returns:
            Dict with key ``text`` (``json`` and ``text`` formats).
            With ``response_format="verbose_json"``: ``text``, ``language``, ``duration``, ``segments``.

        Raises SyncWaitTimeout (504) when no worker finished in time; the job keeps
        running and ``job_id`` identifies it for ``wait_for_job``/``get_result``.
        """
        audio_path = Path(audio_path)
        data: dict = {
            "model": model,
            "response_format": response_format,
            "private": str(private).lower(),
        }
        optional = {
            "language": language,
            "engine": engine,
            "execution_profile": execution_profile,
            "profile": profile,
        }
        data.update({k: v for k, v in optional.items() if v})
        content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        with open(audio_path, "rb") as f:
            resp = self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f, content_type)},
                data=data,
            )
        if resp.status_code == 504:
            code, message = _parse_error_body(resp)
            job_id = _json_field(resp, "job_id")
            if code == "timeout" and isinstance(job_id, int):
                raise SyncWaitTimeout(resp.status_code, code, message, job_id=job_id)
        if resp.is_error:
            _raise_for(resp)
        if response_format == "text":
            return {"text": resp.text}
        return resp.json()

    def create_speech(
        self,
        text: str,
        *,
        model: str = "tts-1",
        voice: str = "alloy",
        response_format: str = "mp3",
        speed: float = 1.0,
        engine: Optional[str] = None,
        private: bool = False,
    ) -> bytes:
        """Synthesise speech from text via the gateway. POST /v1/audio/speech

        Returns:
            Raw audio bytes in the requested format.
        """
        body: dict = {
            "input": text,
            "model": model,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
            "private": private,
        }
        if engine:
            body["engine"] = engine
        return self._send("POST", "/v1/audio/speech", json=body).content

    # ── Profile CRUD ──────────────────────────────────────────────────

    def list_profiles(self) -> list[dict]:
        """List stored profiles. GET /profiles"""
        return self._send("GET", "/profiles").json()["profiles"]

    def get_profile(self, name: str) -> dict:
        """Fetch one profile. GET /profiles/{name}"""
        return self._send("GET", f"/profiles/{name}").json()

    def put_profile(self, name: str, profile: dict) -> dict:
        """Create or replace a profile. PUT /profiles/{name}"""
        return self._send("PUT", f"/profiles/{name}", json=profile).json()

    def delete_profile(self, name: str) -> None:
        """Delete a profile. DELETE /profiles/{name}"""
        self._send("DELETE", f"/profiles/{name}")

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _status_and_error(resp: httpx.Response) -> tuple[int, Optional[str], str]:
    code, message = _parse_error_body(resp)
    return resp.status_code, code, message


def _json_field(resp: httpx.Response, name: str) -> Any:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body.get(name) if isinstance(body, dict) else None


def _settings_from(resp: httpx.Response) -> SettingsDoc:
    body = resp.json()
    etag = resp.headers.get("ETag")
    if etag is None:
        raise ResonaAPIError(resp.status_code, None, "settings response has no ETag")
    return SettingsDoc(
        organization_id=str(body["organization_id"]),
        revision=int(body["revision"]),
        etag=etag,
        settings=dict(body["settings"]),
    )

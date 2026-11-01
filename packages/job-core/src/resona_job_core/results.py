"""Job result contract shared by hub, workers and clients.

Results are validated JSON documents. ``extra='forbid'`` keeps workers from
smuggling undeclared fields (prompts, paths, payloads) into stored results.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Segment(_Strict):
    start: float
    end: float
    text: str


class BBox(_Strict):
    l: float  # noqa: E741 - field name mirrors the Docling/PDF convention
    t: float
    r: float
    b: float
    coord_origin: str


class PageBlock(_Strict):
    text: str
    label: str
    bbox: BBox | None = None


class Page(_Strict):
    number: int  # 1-based
    text: str
    blocks: list[PageBlock] = []


class StageProvenance(_Strict):
    """One executed stage. In-process stages record the accelerator actually used;
    endpoint stages (``llm`` via an approved endpoint) record ``endpoint_id`` and
    ``accelerator='external'`` because the endpoint process owns its device.
    ``accelerator='auto'`` marks an in-process stage whose device the operating
    system selects and does not report (Apple Vision OCR may use CPU, GPU or Neural
    Engine); it records that the device was not observed, not that it was a GPU.

    ``peak_accelerator_bytes`` is the peak device memory (CUDA allocator / MLX) of the
    execution process, recorded on stages that ran on ``cuda``/``metal``; stages run
    together by one engine call (Docling layout + table structure) share the value."""

    stage: Literal["asr", "ocr", "layout", "llm"]
    engine: str
    model: str
    model_revision: str | None = None
    accelerator: Literal["metal", "cuda", "cpu", "auto", "external"]
    endpoint_id: str | None = None
    seconds: float
    peak_accelerator_bytes: int | None = None


class Provenance(_Strict):
    stages: list[StageProvenance] = []
    worker_id: str | None = None
    runtime_version: str | None = None
    settings_revision: int | None = None
    input_sha256: str | None = None


class JobResult(_Strict):
    schema_version: Literal[1] = 1
    kind: Literal["audio", "document"]
    raw_text: str
    markdown: str
    language: str | None = None
    segments: list[Segment] = []
    pages: list[Page] = []
    warnings: list[str] = []
    structured: dict[str, object] = {}
    provenance: Provenance


__all__ = [
    "BBox",
    "JobResult",
    "Page",
    "PageBlock",
    "Provenance",
    "Segment",
    "StageProvenance",
]

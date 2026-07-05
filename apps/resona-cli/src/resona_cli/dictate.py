"""Shared push-to-talk / live-dictation primitives used by `resona live`.

Recording a segment (push-to-talk) or streaming continuously (live mode)
both land text back into an editable markdown buffer at either the position
the cursor was at when recording started, or — if the user has since moved
the cursor or edited elsewhere — at the cursor's current position instead.
The insertion point is tracked through concurrent edits via `AnchorTracker`
(see dictate_anchors.py), keyed off a single-glyph "🎙" placeholder that is
inserted at record-start and replaced once a result is ready.

`DictationTextArea` distinguishes genuine user activity from our own
bookkeeping edits so that one segment's insertion/resolution never looks
like "the user edited the document" to another still-pending segment —
whether that other segment is a discrete push-to-talk recording or a live
stream's next delta. `resolve_anchor()` is the shared decision+mutation
logic for placing text at an anchor (or falling back to the live cursor),
optionally leaving a fresh marker behind for a live segment's next delta.

`TranscriptionWorker` serializes all access to the shared ASR model
(`resona_asr_core.registry.get_transcriber()`) across every open document
and both dictation modes — nothing documents concurrent calls into the same
loaded model instance as safe, and recording is never blocked on
transcription completing, so two documents' work can otherwise overlap.
"""
import contextlib
import json
import threading
import queue
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from textual.widgets import TextArea
from textual.widgets.text_area import Edit, EditResult, Selection

from .dictate_anchors import AnchorTracker, Location

PLACEHOLDER_TEXT = "🎙"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Segment:
    id: str
    wav_path: Path
    started_at: str
    kind: str = "ptt"  # "ptt" (one-shot) | "live" (resolved repeatedly)
    status: str = "recording"  # recording | queued | transcribing | inserted | error
    ended_at: Optional[str] = None
    transcript: Optional[str] = None
    insertion_mode: Optional[str] = None  # "anchor" | "cursor"
    insertion_location: Optional[Location] = None
    error: Optional[str] = None
    disturbed: bool = False  # a real user edit/navigation happened while pending


class DictationTextArea(TextArea):
    """A TextArea that reports every edit (keystrokes and programmatic) to an
    AnchorTracker, and distinguishes genuine user activity (typing,
    navigating) from our own bookkeeping edits (inserting/resolving a
    dictation marker) via `internal_edit()`.

    Every mutation — user keystrokes, paste, and every programmatic
    `insert()`/`replace()`/`delete()` call — routes through `edit()`, and
    every one of those (plus pure cursor navigation, which never calls
    `edit()`) updates the `selection` reactive. Watching `selection` is
    therefore the single place that sees *all* cursor movement.
    """

    def __init__(self, *args, tracker: AnchorTracker, on_user_activity=None, **kwargs):
        # Set before super().__init__(): the base TextArea constructor sets
        # the initial document, which moves the cursor and fires
        # watch_selection() before this subclass would otherwise be ready.
        self._tracker = tracker
        self._on_user_activity = on_user_activity
        self._internal_depth = 0
        super().__init__(*args, **kwargs)

    @contextlib.contextmanager
    def internal_edit(self):
        """Mark edits made in this block as our own bookkeeping, not user activity.

        Without this, starting or resolving one dictation segment would look
        like "the user edited the document" to every *other* still-pending
        segment, wrongly knocking them out of their anchored insertion point.
        """
        self._internal_depth += 1
        try:
            yield
        finally:
            self._internal_depth -= 1

    def edit(self, edit: Edit) -> EditResult:
        top, bottom = edit.top, edit.bottom
        result = super().edit(edit)
        self._tracker.apply_edit(top, bottom, result.end_location)
        return result

    def watch_selection(self, previous_selection: Selection, selection: Selection) -> None:
        if self._internal_depth == 0 and self._on_user_activity is not None:
            self._on_user_activity()

    def action_undo(self) -> None:
        # Edit/undo/redo bypass TextArea.edit() internally, so undoing would
        # silently desync pending segments' tracked anchors. Disabled rather
        # than half-supported.
        self.notify("Undo is disabled during dictation.", severity="warning")

    def action_redo(self) -> None:
        self.notify("Redo is disabled during dictation.", severity="warning")


def resolve_anchor(
    text_area: DictationTextArea,
    span: tuple[Location, Location],
    text: str,
    *,
    disturbed: bool,
    leave_marker: bool,
) -> tuple[str, Location, Optional[tuple[Location, Location]]]:
    """Place `text` at `span`, or at the live cursor if `disturbed`.

    If not disturbed: nothing the user actually did (typing, navigating) has
    touched the document since the span's owner started recording — only
    possibly *other* dictation markers, which don't count (see
    `internal_edit`). Replace the span in place. maintain_selection_offset=True
    keeps the live cursor correctly positioned relative to the edit even
    when other segments' markers sit between this span and the cursor
    (chained concurrent dictation) — for the common case (no siblings) it
    lands the cursor at the end of the inserted text either way, since the
    cursor sits exactly at the edit boundary.

    If disturbed: the user moved the cursor or kept editing while this span
    was pending — drop it and insert at the live cursor instead.
    maintain_selection_offset=True lets Textual's own selection adjustment
    move the live cursor correctly for the deletion.

    If `leave_marker`, a fresh PLACEHOLDER_TEXT is inserted immediately after
    the placed text and its span returned (registering it lets a live
    segment keep resolving its *next* delta the same way, riding forward
    through the document like a normal character). Otherwise `None` is
    returned — the caller is done with this span for good (push-to-talk).

    Returns `(mode, location, new_span)` where `mode` is `"anchor"` or
    `"cursor"` and `location` is where `text` landed.
    """
    start, end = span
    if not disturbed:
        with text_area.internal_edit():
            result = text_area.replace(text, start, end, maintain_selection_offset=True)
        mode, location = "anchor", start
    else:
        with text_area.internal_edit():
            text_area.delete(start, end, maintain_selection_offset=True)
            insertion_point = text_area.cursor_location
            result = text_area.insert(text, insertion_point, maintain_selection_offset=False)
        mode, location = "cursor", insertion_point

    new_span = None
    if leave_marker:
        with text_area.internal_edit():
            marker = text_area.insert(PLACEHOLDER_TEXT, result.end_location, maintain_selection_offset=False)
        new_span = (result.end_location, marker.end_location)
    return mode, location, new_span


def transcribe_segment(engine, segment: Segment, language: str) -> None:
    """Run `engine` on `segment.wav_path`, mutating `segment` in place.

    Meant to run inside a `TranscriptionWorker` job — synchronous, blocking.
    """
    segment.status = "transcribing"
    try:
        result = engine.transcribe(segment.wav_path, language=language)
        segment.transcript = (result.get("text") or "").strip()
        segment.status = "transcribed"
    except Exception as e:
        segment.error = str(e)
        segment.status = "error"
    segment.ended_at = _now_iso()


class TranscriptionWorker:
    """Serializes arbitrary jobs onto a single background thread.

    A single thread (not one per job): nothing in resona-asr-core documents
    concurrent calls into the same loaded model instance as safe, and this
    is shared app-wide across every open document and both push-to-talk and
    local live-mode transcription. Recording itself is never blocked —
    jobs just queue.
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], None]) -> None:
        self._queue.put(job)

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            job()


def run_serialized(worker: TranscriptionWorker, fn: Callable[[], Any]) -> Any:
    """Run `fn()` on `worker`'s thread and block the calling thread until done.

    Used by local live-mode's per-document polling loop so its
    `process_sync()`/`flush_sync()` calls (which touch the shared ASR model)
    serialize against push-to-talk segments and other documents' live
    streams, without blocking *this* document's audio capture — only the
    model-access call itself waits.
    """
    done = threading.Event()
    box: dict = {}

    def job() -> None:
        try:
            box["result"] = fn()
        except Exception as e:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = e
        finally:
            done.set()

    worker.submit(job)
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("result")

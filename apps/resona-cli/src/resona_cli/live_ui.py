"""Resona Live TUI: multi-document dictation.

Each open document is a tab. By default (Live switch off) a document uses
push-to-talk: tap F8 to record a segment, tap again to stop, and the
transcript lands back in the buffer once ready — see `dictate.py` for the
anchor-tracking mechanics that make this safe under concurrent edits.
Flipping a document's Live switch on switches it to continuous streaming
transcription instead: confirmed transcript chunks are spliced into the
same buffer as they arrive, using the same anchor-tracking (a live segment
just keeps leaving a fresh marker behind after each delta instead of
resolving once).

New documents are created via `ctrl+n` (or the startup prompt when `resona
live` is launched with no file), each autosaves independently, and `ctrl+n`/
`ctrl+w`/`ctrl+pagedown`/`ctrl+pageup` manage/switch between them.
"""
import asyncio
import json
import logging
import os
import threading
import queue
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soxr

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static, Switch, TabbedContent, TabPane

from .dictate import (
    DictationTextArea,
    PLACEHOLDER_TEXT,
    Segment,
    TranscriptionWorker,
    _now_iso,
    resolve_anchor,
    run_serialized,
    transcribe_segment,
)
from .dictate_anchors import AnchorTracker
from .markdown_output import read_markdown, write_markdown
from .micrec import RecordingSession
from resona_asr_core.live_transcriber import LiveTranscriber
from resona_asr_core.audio import SAMPLE_RATE as ASR_SAMPLE_RATE

# Audio capture settings (must match recorder defaults)
MIC_SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
MIC_CHANNELS = int(os.getenv("CHANNELS", 1))
MIC_BLOCK_SIZE = 1024

# Resample mic audio to the ASR sample rate when the two differ.
_NEEDS_RESAMPLE = MIC_SAMPLE_RATE != ASR_SAMPLE_RATE


def _resample_to_asr(audio_float: np.ndarray) -> np.ndarray:
    """Resample a mono float32 chunk from the mic rate to the ASR rate.

    Returns the input unchanged when the rates already match.
    """
    if not _NEEDS_RESAMPLE:
        return audio_float
    return soxr.resample(audio_float, MIC_SAMPLE_RATE, ASR_SAMPLE_RATE)


class NewFileModal(ModalScreen):
    """Prompt for a filename. Dismisses with the entered path, or None if cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, allow_cancel: bool = True) -> None:
        super().__init__()
        self._allow_cancel = allow_cancel

    def compose(self) -> ComposeResult:
        with Vertical(id="new_file_dialog"):
            yield Static("New document — enter a filename:")
            yield Input(placeholder="notes.md", id="new_file_input")
            with Horizontal(id="new_file_buttons"):
                if self._allow_cancel:
                    yield Button("Cancel", id="cancel_button")
                yield Button("Create", id="create_button", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#new_file_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create_button":
            self._submit()
        elif event.button.id == "cancel_button":
            self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#new_file_input", Input).value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        if self._allow_cancel:
            self.dismiss(None)


class DocumentPane(TabPane):
    """One open document: an anchor-tracked editable buffer plus a Live switch.

    Duck-types the `app_ref` interface `RecordingSession` expects
    (`status_message`, `is_recording`, `is_paused`, `call_from_thread`,
    `set_status_from_callback`) so each pane's recording state is fully
    independent of every other open document.
    """

    status_message = reactive("Loading engine…")

    def __init__(
        self, file: Path, *, id: str, engine_name: Optional[str], language: str,
        force_live: bool = False,
    ) -> None:
        super().__init__(file.name, id=id)
        self.file = file
        self.engine_name = engine_name
        self.language = language
        self.force_live = force_live
        self.is_live = force_live
        self.is_recording = False
        self.is_paused = False

        self.tracker = AnchorTracker()
        self.segments: dict[str, Segment] = {}
        self.segment_counter = 0
        self.dictation_dir = file.parent / f"{file.stem}.dictation"
        self.manifest_path = self.dictation_dir / "manifest.json"

        self.session: Optional[RecordingSession] = None
        self.active_segment_id: Optional[str] = None

        # Live-mode-only transient state.
        self.live_segment_id: Optional[str] = None
        self.live_transcriber = None
        self.live_thread: Optional[threading.Thread] = None
        self.live_stop_event = threading.Event()
        self.audio_queue: "queue.Queue" = queue.Queue()
        self.live_feed_timer = None
        self._local_worker: Optional[TranscriptionWorker] = None

        self.text_area: Optional[DictationTextArea] = None
        self.switch: Optional[Switch] = None
        self._status_static: Optional[Static] = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="pane_header"):
            yield Static(self.file.name, id="pane_filename")
            self.switch = Switch(value=self.is_live, disabled=self.force_live, id="live_switch")
            yield self.switch
            yield Static("Live", classes="switch_label")
            self._status_static = Static(self.status_message, id="pane_status")
            yield self._status_static
        self.text_area = DictationTextArea(
            tracker=self.tracker, on_user_activity=self._on_user_activity, id="doc_area",
        )
        yield self.text_area

    def on_mount(self) -> None:
        body = ""
        if self.file.exists():
            body, _meta = read_markdown(self.file)
        # Independent of the .md file: a prior session may have recorded
        # segments (and written manifest.json) without ever having saved yet.
        self._load_manifest()
        self.text_area.load_text(body)

    def watch_status_message(self, new_message: str) -> None:
        if self._status_static is not None:
            try:
                self._status_static.update(new_message)
            except Exception:
                pass

    def on_switch_changed(self, event: Switch.Changed) -> None:
        event.stop()
        if self.is_recording:
            # Revert without posting a new Changed message: assigning
            # `.value` normally would queue another Changed message that's
            # processed later (async), by which point a naive suppress-flag
            # would already have been reset — the two messages ping-pong
            # forever. set_reactive() bypasses the watcher/message entirely;
            # the slider position is updated manually to match.
            switch = event.switch
            switch.set_reactive(Switch.value, not event.value)
            switch._slider_position = 0.0 if event.value else 1.0
            self.app.notify("Stop recording before switching modes.", severity="warning")
            return
        self.is_live = event.value

    # ── RecordingSession's app_ref duck-type ──────────────────────────

    def call_from_thread(self, fn, *args, **kwargs):
        return self.app.call_from_thread(fn, *args, **kwargs)

    def set_status_from_callback(self, message: str) -> None:
        self.status_message = message

    # ── shared segment bookkeeping ────────────────────────────────────

    def _on_user_activity(self) -> None:
        """A genuine keystroke or cursor move happened (not our own bookkeeping).

        Disqualifies every currently in-flight segment's anchor: they'll be
        inserted at the live cursor instead of replacing their marker in place.
        """
        for segment in self.segments.values():
            if segment.status in ("recording", "queued", "transcribing"):
                segment.disturbed = True

    def _insert_marker(self, kind: str) -> Segment:
        self.segment_counter += 1
        segment_id = f"{self.segment_counter:04d}"
        self.dictation_dir.mkdir(parents=True, exist_ok=True)
        wav_path = self.dictation_dir / f"segment-{segment_id}.wav"

        start_loc = self.text_area.cursor_location
        with self.text_area.internal_edit():
            result = self.text_area.insert(PLACEHOLDER_TEXT, start_loc, maintain_selection_offset=False)
        self.tracker.register(segment_id, start_loc, result.end_location)

        segment = Segment(id=segment_id, wav_path=wav_path, started_at=_now_iso(), kind=kind)
        self.segments[segment_id] = segment
        self._write_manifest()
        return segment

    # ── push-to-talk ─────────────────────────────────────────────────

    async def start_segment(self) -> None:
        segment = self._insert_marker("ptt")
        self.active_segment_id = segment.id
        self.session = RecordingSession(filename=str(segment.wav_path))
        self.is_recording = True
        self.status_message = f"\U0001f534 Recording segment {segment.id}…"
        self.session.start(self)

    async def stop_segment(self, worker: TranscriptionWorker, engine, language: str) -> None:
        session = self.session
        segment_id = self.active_segment_id
        if session is None or segment_id is None:
            return

        session.stop()
        while not session.save_finished_event.is_set():
            await asyncio.sleep(0.05)
        session.join(timeout=2.0)

        self.is_recording = False
        self.session = None
        self.active_segment_id = None

        segment = self.segments[segment_id]
        segment.status = "queued"
        self.status_message = f"Transcribing segment {segment_id}…"
        self._write_manifest()

        def job() -> None:
            transcribe_segment(engine, segment, language)
            self.call_from_thread(self._apply_segment_result, segment)

        worker.submit(job)

    def _apply_segment_result(self, segment: Segment) -> None:
        span = self.tracker.pop(segment.id)

        if segment.status == "error":
            if span is not None:
                start, end = span
                with self.text_area.internal_edit():
                    self.text_area.replace(
                        f"⟦dictation failed: {segment.error}⟧ ", start, end,
                        maintain_selection_offset=True,
                    )
            self.status_message = f"Segment {segment.id} failed: {segment.error}"
            self._write_manifest()
            return

        transcript = segment.transcript or ""
        insert_text = f"{transcript} " if transcript else ""
        if span is None:
            # Anchor vanished unexpectedly (shouldn't happen) — fall back to
            # inserting at the live cursor rather than losing the transcript.
            with self.text_area.internal_edit():
                self.text_area.insert(insert_text, maintain_selection_offset=False)
            mode, location = "cursor", self.text_area.cursor_location
        else:
            mode, location, _ = resolve_anchor(
                self.text_area, span, insert_text, disturbed=segment.disturbed, leave_marker=False,
            )

        segment.status = "inserted"
        segment.insertion_mode = mode
        segment.insertion_location = location
        self.status_message = f"Inserted segment {segment.id}."
        self._write_manifest()
        self.autosave()

    # ── live streaming ───────────────────────────────────────────────

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        """Audio observer callback — receives each chunk from RecordingSession."""
        if self.is_recording:
            self.audio_queue.put(chunk)

    async def start_live(
        self, worker: Optional[TranscriptionWorker], language: str,
        remote: Optional[str], remote_engine: Optional[str],
    ) -> None:
        segment = self._insert_marker("live")
        self.live_segment_id = segment.id

        self.session = RecordingSession(filename=str(segment.wav_path))
        self.session.add_audio_observer(self._on_audio_chunk)
        self.is_recording = True
        self.status_message = "\U0001f534 Live…"
        self.session.start(self)

        self.live_stop_event.clear()
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        if remote and remote_engine:
            from .remote_live import GatewayLiveTranscriber
            self._local_worker = None
            self.live_transcriber = GatewayLiveTranscriber(remote, engine=remote_engine, language=language)
            self.live_transcriber.start()
        elif remote:
            from .remote_live import RemoteLiveTranscriber
            self._local_worker = None
            self.live_transcriber = RemoteLiveTranscriber(remote, language=language)
            self.live_transcriber.start()
        else:
            self._local_worker = worker
            self.live_transcriber = LiveTranscriber(language=language)

        self.live_thread = threading.Thread(target=self._live_worker_loop, daemon=True)
        self.live_thread.start()
        self.live_feed_timer = self.set_interval(0.05, self._feed_audio_to_transcriber)

    async def stop_live(self) -> None:
        self.live_stop_event.set()
        if self.live_feed_timer is not None:
            self.live_feed_timer.stop()
            self.live_feed_timer = None
        if self.session is not None:
            self.session.remove_audio_observer(self._on_audio_chunk)
        if self.live_thread and self.live_thread.is_alive():
            # Off the UI thread: this waits for the worker's final flush,
            # which itself calls back into the UI thread via
            # call_from_thread — awaiting a plain join here would deadlock.
            await asyncio.to_thread(self.live_thread.join, 3.0)

        session = self.session
        if session is not None:
            session.stop()
            while not session.save_finished_event.is_set():
                await asyncio.sleep(0.05)
            session.join(timeout=2.0)

        self.is_recording = False
        self.session = None
        self.live_segment_id = None
        self.live_transcriber = None
        self.live_thread = None

    def _feed_audio_to_transcriber(self) -> None:
        """Timer callback: read audio from the local queue and feed the transcriber."""
        if self.live_transcriber is None:
            return
        chunks_processed = 0
        while not self.audio_queue.empty() and chunks_processed < 50:
            try:
                chunk = self.audio_queue.get_nowait()
            except queue.Empty:
                break
            if chunk.shape[1] > 1:
                chunk = chunk.mean(axis=1, keepdims=True)
            audio_float = chunk.flatten().astype(np.float32)
            self.live_transcriber.add_audio(_resample_to_asr(audio_float))
            chunks_processed += 1

    def _live_worker_loop(self) -> None:
        """Background thread: periodically process audio and post results to the UI."""
        while not self.live_stop_event.is_set():
            signalled = self.live_transcriber._audio_event_sync.wait(timeout=1.0)
            if signalled:
                self.live_transcriber._audio_event_sync.clear()

            if not self.live_transcriber.has_enough_audio():
                continue

            try:
                result = self._process_sync()
                if result is None:
                    continue
                self.call_from_thread(
                    self._apply_live_result, result.confirmed_delta, result.partial, False, "",
                )
            except Exception as e:
                self.call_from_thread(self._notify_error, f"Live transcription error: {e}")

        try:
            result = self._flush_sync()
            self.call_from_thread(
                self._apply_live_result, result.confirmed_delta, "", True, result.confirmed,
            )
        except Exception as e:
            self.call_from_thread(self._notify_error, f"Live flush error: {e}")

    def _process_sync(self):
        if self._local_worker is not None:
            return run_serialized(self._local_worker, self.live_transcriber.process_sync)
        return self.live_transcriber.process_sync()

    def _flush_sync(self):
        if self._local_worker is not None:
            return run_serialized(self._local_worker, self.live_transcriber.flush_sync)
        return self.live_transcriber.flush_sync()

    def _notify_error(self, message: str) -> None:
        self.app.notify(message, severity="error")

    def _apply_live_result(
        self, confirmed_delta: str, partial: str, is_final: bool, full_confirmed: str,
    ) -> None:
        segment_id = self.live_segment_id
        segment = self.segments.get(segment_id) if segment_id else None
        span = self.tracker.get(segment_id) if segment_id else None

        if segment is not None and span is not None and (confirmed_delta or is_final):
            self.tracker.pop(segment_id)
            insert_text = f"{confirmed_delta.strip()} " if confirmed_delta else ""
            mode, location, new_span = resolve_anchor(
                self.text_area, span, insert_text,
                disturbed=segment.disturbed, leave_marker=not is_final,
            )
            segment.disturbed = False
            if confirmed_delta:
                segment.transcript = f"{segment.transcript or ''}{insert_text}"
                segment.insertion_mode = mode
                segment.insertion_location = location
            if new_span is not None:
                self.tracker.register(segment_id, *new_span)
            if is_final:
                segment.status = "inserted"
                segment.ended_at = _now_iso()
            self._write_manifest()
            self.autosave()

        if is_final:
            self.status_message = "Ready. F8 to record."
        elif partial:
            self.status_message = f"\U0001f534 Live: {partial}"
        else:
            self.status_message = "\U0001f534 Live…"

    # ── persistence ──────────────────────────────────────────────────

    def _document_meta(self) -> dict:
        return {
            "title": self.file.stem,
            "engine": self.engine_name,
            "language": self.language,
            "segments": len(self.segments),
        }

    def autosave(self) -> None:
        write_markdown(self.file, self.text_area.text, self._document_meta())

    def _write_manifest(self) -> None:
        manifest = {
            "version": 1,
            "document": self.file.name,
            "segments": [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "wav": s.wav_path.name,
                    "started_at": s.started_at,
                    "ended_at": s.ended_at,
                    "engine": self.engine_name,
                    "status": s.status,
                    "transcript": s.transcript,
                    "insertion": (
                        {"mode": s.insertion_mode, "location": list(s.insertion_location)}
                        if s.insertion_location is not None else None
                    ),
                    "error": s.error,
                }
                for s in self.segments.values()
            ],
        }
        self.dictation_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.manifest_path)

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        max_id = 0
        for entry in data.get("segments", []):
            try:
                max_id = max(max_id, int(entry["id"]))
            except (KeyError, TypeError, ValueError):
                continue
        self.segment_counter = max_id


class LiveApp(App):
    CSS_PATH = "live.tcss"
    TITLE = "Resona Live"

    BINDINGS = [
        Binding("f8", "toggle_recording", "Record/Stop", priority=True, show=True),
        Binding("ctrl+n", "new_file", "New File", priority=True, show=True),
        Binding("ctrl+w", "close_tab", "Close Tab", priority=True, show=True),
        Binding("ctrl+pagedown", "next_tab", "Next Tab", priority=True, show=False),
        Binding("ctrl+pageup", "previous_tab", "Previous Tab", priority=True, show=False),
        Binding("ctrl+l", "toggle_live", "Toggle Live", priority=True, show=True),
        Binding("ctrl+s", "save_document", "Save", priority=True, show=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True, show=True),
        Binding("ctrl+c", "request_quit", "Force Quit", priority=True, show=False),
    ]

    def __init__(
        self, file: Optional[Path], engine: Optional[str], language: str,
        remote: Optional[str] = None, remote_engine: Optional[str] = None,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self._initial_file = file
        self.engine_name = engine
        self.language = language
        self.remote = remote
        self.remote_engine = remote_engine
        # Named debug_logs, not debug: textual.app.App.debug is a read-only
        # property (devtools mode) and would collide.
        self.debug_logs = debug
        self.worker: Optional[TranscriptionWorker] = None  # local mode only
        self._engine = None
        self._detected_engine_url: Optional[str] = None  # auto-detected local engine-server
        self._pane_counter = 0
        self._recording_pane_id: Optional[str] = None
        self._panes: dict[str, DocumentPane] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield TabbedContent(id="documents")
        yield Footer()

    async def on_mount(self) -> None:
        if self.debug_logs:
            tabs = self.query_one("#documents", TabbedContent)
            await tabs.add_pane(TabPane("Logs", RichLog(id="log_display", wrap=True), id="logs_tab"))
            self._setup_debug_logging()

        if self._initial_file is not None:
            await self._open_document(self._initial_file)
        else:
            self.push_screen(NewFileModal(allow_cancel=False), self._on_initial_file_named)

        if not self.remote:
            threading.Thread(target=self._load_engine, daemon=True).start()

    def _setup_debug_logging(self) -> None:
        """Capture root-logger output into the Logs tab (--debug only)."""
        app = self

        class _TUILogHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = self.format(record)
                try:
                    app.call_from_thread(app.log_msg, f"[dim]{record.name}:[/dim] {msg}")
                except RuntimeError:
                    pass  # called from the UI thread itself — drop rather than deadlock

        root_logger = logging.getLogger()
        handler = _TUILogHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        self.log_msg("Debug logging enabled.")

    def log_msg(self, msg: str) -> None:
        """Write a timestamped message to the Logs tab (--debug only)."""
        if not self.debug_logs:
            return
        try:
            log_display = self.query_one("#log_display", RichLog)
            timestamp = time.strftime("%H:%M:%S")
            log_display.write(f"[{timestamp}] {msg}")
        except Exception:
            pass

    def _on_initial_file_named(self, filename: Optional[str]) -> None:
        if not filename:
            self.exit()
            return
        self.run_worker(self._open_document(Path(filename)))

    def _on_new_file_named(self, filename: Optional[str]) -> None:
        if filename:
            self.run_worker(self._open_document(Path(filename)))

    async def _open_document(self, path: Path) -> None:
        self._pane_counter += 1
        pane_id = f"doc_{self._pane_counter}"
        pane = DocumentPane(
            path, id=pane_id, engine_name=self.engine_name, language=self.language,
            force_live=bool(self.remote),
        )
        if self.remote or self.worker is not None:
            # Either explicit --remote, or the shared engine already finished
            # loading before this document was opened (e.g. via ctrl+n).
            pane.status_message = "Ready. F8 to record."
        self._panes[pane_id] = pane

        tabs = self.query_one("#documents", TabbedContent)
        await tabs.add_pane(pane, before="logs_tab" if self.debug_logs else None)
        tabs.active = pane_id
        pane.text_area.focus()

    # ── engine loading ───────────────────────────────────────────────

    def _load_engine(self) -> None:
        from .local_engine import DEFAULT_LOCAL_ENGINE_PORT, LocalEngine, probe_local_engine
        if probe_local_engine():
            self._engine = LocalEngine(engine=self.engine_name or "faster-whisper").__enter__()
            self._detected_engine_url = f"ws://localhost:{DEFAULT_LOCAL_ENGINE_PORT}"
            self.worker = TranscriptionWorker()
            self.call_from_thread(
                self._on_engine_ready, f"Using local engine on :{DEFAULT_LOCAL_ENGINE_PORT}.",
            )
            return

        from .engine import InProcessEngine
        try:
            engine = InProcessEngine(self.engine_name)
        except Exception as e:
            self.call_from_thread(self._on_engine_load_error, str(e))
            return
        self._engine = engine
        self.worker = TranscriptionWorker()
        self.call_from_thread(self._on_engine_ready, "Ready.")

    def _on_engine_ready(self, note: str = "Ready.") -> None:
        for pane in self._panes.values():
            if pane.status_message == "Loading engine…":
                pane.status_message = f"{note} F8 to record."

    def _on_engine_load_error(self, message: str) -> None:
        for pane in self._panes.values():
            pane.status_message = f"Engine failed to load: {message}"

    # ── tab management ───────────────────────────────────────────────

    def _active_pane(self) -> Optional[DocumentPane]:
        tabs = self.query_one("#documents", TabbedContent)
        pane = tabs.active_pane
        return pane if isinstance(pane, DocumentPane) else None

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if isinstance(event.pane, DocumentPane) and event.pane.text_area is not None:
            event.pane.text_area.focus()

    async def action_new_file(self) -> None:
        self.push_screen(NewFileModal(), self._on_new_file_named)

    async def action_close_tab(self) -> None:
        pane = self._active_pane()
        if pane is None:
            return
        if pane.is_recording:
            self.notify("Stop recording before closing this tab.", severity="warning")
            return
        if len(self._panes) <= 1:
            self.notify("Can't close the last open document.", severity="warning")
            return
        pane.autosave()
        tabs = self.query_one("#documents", TabbedContent)
        await tabs.remove_pane(pane.id)
        self._panes.pop(pane.id, None)

    def _cycle_tab(self, offset: int) -> None:
        ids = list(self._panes.keys())
        if not ids:
            return
        tabs = self.query_one("#documents", TabbedContent)
        current = tabs.active
        idx = ids.index(current) if current in ids else 0
        tabs.active = ids[(idx + offset) % len(ids)]

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_previous_tab(self) -> None:
        self._cycle_tab(-1)

    def action_toggle_live(self) -> None:
        pane = self._active_pane()
        if pane is None or pane.force_live:
            return
        if pane.is_recording:
            self.notify("Stop recording before switching modes.", severity="warning")
            return
        pane.switch.value = not pane.switch.value

    # ── recording ────────────────────────────────────────────────────

    async def action_toggle_recording(self) -> None:
        pane = self._active_pane()
        if pane is None:
            return

        if pane.is_recording:
            if pane.is_live:
                await pane.stop_live()
            else:
                await pane.stop_segment(self.worker, self._engine, self.language)
            self._recording_pane_id = None
            return

        if self._recording_pane_id is not None:
            self.notify("Already recording in another tab — stop it first.", severity="warning")
            return

        if pane.is_live:
            if not self.remote and self.worker is None:
                self.notify("Engine is still loading…", severity="warning")
                return
            effective_remote = self.remote or self._detected_engine_url
            await pane.start_live(self.worker, self.language, effective_remote, self.remote_engine)
        else:
            if self._engine is None:
                self.notify("Engine is still loading…", severity="warning")
                return
            await pane.start_segment()
        self._recording_pane_id = pane.id

    # ── save / quit ──────────────────────────────────────────────────

    def action_save_document(self) -> None:
        pane = self._active_pane()
        if pane is None:
            return
        pane.autosave()
        self.notify("Saved.", title="Saved")

    async def action_request_quit(self) -> None:
        for pane in list(self._panes.values()):
            if pane.is_recording:
                if pane.is_live:
                    await pane.stop_live()
                else:
                    await pane.stop_segment(self.worker, self._engine, self.language)
            pane.autosave()
        if self.worker is not None:
            self.worker.stop()
        self.exit()

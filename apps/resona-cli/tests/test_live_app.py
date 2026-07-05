"""Tests for resona_cli.live_ui — LiveApp/DocumentPane (no real audio hardware, no real ASR model)."""
import asyncio
import json
import queue
import threading
from unittest.mock import patch

import pytest

pytest.importorskip("sounddevice", reason="sounddevice not installed; reinstall resona-cli")
pytest.importorskip("textual", reason="textual not installed; reinstall resona-cli")

from resona_asr_core.live_transcriber import TranscriptionResult
from resona_cli.live_ui import LiveApp, NewFileModal, PLACEHOLDER_TEXT
from textual.widgets import RichLog, Tab


@pytest.fixture(autouse=True)
def _no_local_engine_by_default(monkeypatch):
    """These tests assume no engine-server is already running on the
    detection port, regardless of the host machine's actual local state —
    tests for the auto-detection feature itself override this explicitly."""
    monkeypatch.setattr("resona_cli.local_engine.probe_local_engine", lambda *a, **k: False)


class _FakeSession:
    """Stand-in for RecordingSession: no real audio I/O, completes instantly."""

    def __init__(self, filename):
        self.filename = filename
        self.save_finished_event = threading.Event()
        self.thread = None

    def start(self, app_ref):
        pass

    def stop(self):
        self.save_finished_event.set()

    def join(self, timeout=None):
        pass

    def add_audio_observer(self, callback):
        pass

    def remove_audio_observer(self, callback):
        pass


class _FakeWorker:
    """Stand-in for TranscriptionWorker: records submissions but never
    auto-resolves them, so tests control exactly when/how each segment
    completes via pane._apply_segment_result() instead of racing a real
    background thread."""

    def __init__(self, *args, **kwargs):
        self.submitted = []

    def submit(self, job):
        self.submitted.append(job)

    def stop(self):
        pass


class _FakeLiveTranscriber:
    """Test double honoring the pull-based LiveTranscriber surface."""

    def __init__(self, language="de", **kwargs):
        self.language = language
        self._audio_event_sync = threading.Event()
        self._queue: "queue.Queue" = queue.Queue()
        self._flush_result = TranscriptionResult(confirmed="", partial="", language=language, confirmed_delta="")

    def add_audio(self, audio):
        pass

    def has_enough_audio(self):
        return not self._queue.empty()

    def process_sync(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def flush_sync(self):
        return self._flush_result

    def get_full_transcript(self):
        return ""

    # test helpers
    def push_delta(self, confirmed_delta, partial=""):
        self._queue.put(TranscriptionResult(
            confirmed="", partial=partial, language=self.language, confirmed_delta=confirmed_delta,
        ))
        self._audio_event_sync.set()

    def set_flush_result(self, confirmed_delta, confirmed=""):
        self._flush_result = TranscriptionResult(
            confirmed=confirmed, partial="", language=self.language, confirmed_delta=confirmed_delta,
        )


async def _wait_until(condition, timeout=2.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
def app(tmp_path):
    doc = tmp_path / "notes.md"
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.TranscriptionWorker", side_effect=_FakeWorker):
        yield LiveApp(file=doc, engine="faster-whisper", language="de")


@pytest.mark.anyio
async def test_typing_into_editor_works(app):
    async with app.run_test() as pilot:
        await pilot.press(*list("hi quick fox"))
        pane = app._active_pane()
        assert pane.text_area.text == "hi quick fox"


@pytest.mark.anyio
async def test_toggle_recording_inserts_placeholder_and_writes_manifest(app):
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("hello "))
            await pilot.press("f8")
            pane = app._active_pane()
            assert pane.is_recording
            assert PLACEHOLDER_TEXT in pane.text_area.text
            manifest = json.loads(pane.manifest_path.read_text())
            assert manifest["segments"][0]["status"] == "recording"
            assert manifest["segments"][0]["kind"] == "ptt"

            await pilot.press("f8")
            assert not pane.is_recording
            manifest = json.loads(pane.manifest_path.read_text())
            assert manifest["segments"][0]["status"] == "queued"


@pytest.mark.anyio
async def test_insertion_when_cursor_unchanged_lands_at_anchor_and_cursor_moves_to_end(app):
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("before "))
            await pilot.press("f8")  # start: placeholder inserted right after "before "
            pane = app._active_pane()
            segment_id = pane.active_segment_id
            await pilot.press("f8")  # stop: queued, nothing else touches the buffer

            segment = pane.segments[segment_id]
            segment.transcript = "dictated text"
            segment.status = "transcribed"
            pane._apply_segment_result(segment)  # simulate the worker's completion callback

            assert pane.text_area.text == "before dictated text "
            assert pane.text_area.cursor_location == (0, len("before dictated text "))
            assert segment.insertion_mode == "anchor"


@pytest.mark.anyio
async def test_insertion_when_cursor_moved_lands_at_current_cursor(app):
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("before "))
            await pilot.press("f8")
            pane = app._active_pane()
            segment_id = pane.active_segment_id
            await pilot.press("f8")

            # User navigates away and keeps typing elsewhere while it transcribes.
            await pilot.press("home")
            await pilot.press(*list("START "))

            segment = pane.segments[segment_id]
            segment.transcript = "dictated text"
            segment.status = "transcribed"
            pane._apply_segment_result(segment)

            assert pane.text_area.text == "START dictated text before "
            assert segment.insertion_mode == "cursor"


@pytest.mark.anyio
async def test_typing_right_after_a_pending_placeholder_is_not_swallowed(app):
    """Regression test for the span-engulfing bug (see dictate_anchors.py):
    text typed immediately after a still-pending placeholder must survive
    once that segment's transcript is inserted."""
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("AAA "))
            await pilot.press("f8")
            pane = app._active_pane()
            segment_id = pane.active_segment_id
            await pilot.press("f8")

            # Cursor is sitting right at the end of the placeholder; keep typing.
            await pilot.press(*list("BBB "))

            segment = pane.segments[segment_id]
            segment.transcript = "one"
            segment.status = "transcribed"
            pane._apply_segment_result(segment)

            assert pane.text_area.text == "AAA BBB one "
            assert segment.insertion_mode == "cursor"


@pytest.mark.anyio
async def test_two_chained_segments_land_in_dictation_order_when_resolved_in_order(app):
    """Regression test: starting a second segment while the first is still
    pending must NOT count as "the user edited the document" for the first
    one — else the first segment's transcript would land after the second
    segment's marker instead of before it (the reverse of dictation order)."""
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("AAA "))
            await pilot.press("f8")
            pane = app._active_pane()
            seg1_id = pane.active_segment_id
            await pilot.press("f8")  # seg1 queued; cursor sits right after its placeholder

            await pilot.press("f8")  # seg2's placeholder inserted back-to-back with seg1's
            seg2_id = pane.active_segment_id
            await pilot.press("f8")  # seg2 queued too

            assert seg1_id != seg2_id
            assert not pane.segments[seg1_id].disturbed

            seg1 = pane.segments[seg1_id]
            seg1.transcript, seg1.status = "first", "transcribed"
            pane._apply_segment_result(seg1)
            assert seg1.insertion_mode == "anchor"

            seg2 = pane.segments[seg2_id]
            seg2.transcript, seg2.status = "second", "transcribed"
            pane._apply_segment_result(seg2)
            assert seg2.insertion_mode == "anchor"

            assert pane.text_area.text == "AAA first second "


@pytest.mark.anyio
async def test_two_chained_segments_land_in_dictation_order_when_resolved_out_of_order(app):
    """Same as above, but the more recently started segment's transcription
    comes back first — dictation order (not completion order) must win."""
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press(*list("AAA "))
            await pilot.press("f8")
            pane = app._active_pane()
            seg1_id = pane.active_segment_id
            await pilot.press("f8")

            await pilot.press("f8")
            seg2_id = pane.active_segment_id
            await pilot.press("f8")

            seg2 = pane.segments[seg2_id]
            seg2.transcript, seg2.status = "second", "transcribed"
            pane._apply_segment_result(seg2)
            assert seg2.insertion_mode == "anchor"

            seg1 = pane.segments[seg1_id]
            seg1.transcript, seg1.status = "first", "transcribed"
            pane._apply_segment_result(seg1)
            assert seg1.insertion_mode == "anchor"

            assert pane.text_area.text == "AAA first second "


class _FakeAttachedEngine:
    """Stand-in for local_engine.LocalEngine() in attached mode."""

    def __init__(self, *args, **kwargs):
        from unittest.mock import MagicMock
        self.transcribe = MagicMock(return_value={"text": "from server", "language": "de", "segments": []})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.anyio
async def test_detected_local_engine_used_for_push_to_talk_instead_of_in_process(tmp_path):
    """When a local engine-server is already running, use it via HTTP for
    push-to-talk instead of loading an in-process model."""
    from resona_cli.local_engine import DEFAULT_LOCAL_ENGINE_PORT

    doc = tmp_path / "notes.md"
    fake_attached = _FakeAttachedEngine()
    with patch("resona_cli.engine.InProcessEngine") as mock_in_process, \
         patch("resona_cli.local_engine.probe_local_engine", return_value=True), \
         patch("resona_cli.local_engine.LocalEngine", return_value=fake_attached), \
         patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        app = LiveApp(file=doc, engine="faster-whisper", language="de")
        async with app.run_test() as pilot:
            await _wait_until(lambda: app._engine is not None)
            mock_in_process.assert_not_called()
            assert app._detected_engine_url == f"ws://localhost:{DEFAULT_LOCAL_ENGINE_PORT}"

            await pilot.press(*list("hi "))
            await pilot.press("f8")
            pane = app._active_pane()
            segment_id = pane.active_segment_id
            await pilot.press("f8")
            await _wait_until(lambda: pane.segments[segment_id].status == "inserted")

            fake_attached.transcribe.assert_called_once()
            assert "from server" in pane.text_area.text


@pytest.mark.anyio
async def test_manifest_segment_counter_resumes_on_reopen(tmp_path):
    doc = tmp_path / "notes.md"
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.TranscriptionWorker", side_effect=_FakeWorker), \
         patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        app1 = LiveApp(file=doc, engine="faster-whisper", language="de")
        async with app1.run_test() as pilot:
            await pilot.press("f8")
            await pilot.press("f8")
            assert app1._active_pane().segment_counter == 1

        app2 = LiveApp(file=doc, engine="faster-whisper", language="de")
        async with app2.run_test() as pilot:
            await pilot.press("f8")
            await pilot.press("f8")
            assert app2._active_pane().segment_counter == 2


@pytest.mark.anyio
async def test_new_file_creates_second_tab_and_switches_to_it(app, tmp_path):
    async with app.run_test() as pilot:
        await pilot.press("ctrl+n")
        assert isinstance(app.screen, NewFileModal)

        second = str(tmp_path / "second.md")
        await pilot.press(*list(second))
        await pilot.press("enter")
        await _wait_until(lambda: len(app._panes) == 2)

        assert app._active_pane().file == tmp_path / "second.md"


@pytest.mark.anyio
async def test_tab_switching_cycles_between_documents(app, tmp_path):
    async with app.run_test() as pilot:
        first_id = app._active_pane().id
        await app._open_document(tmp_path / "second.md")
        second_id = app._active_pane().id
        assert first_id != second_id

        await pilot.press("ctrl+pageup")
        assert app._active_pane().id == first_id

        await pilot.press("ctrl+pagedown")
        assert app._active_pane().id == second_id


@pytest.mark.anyio
async def test_live_switch_blocked_while_recording(app):
    with patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        async with app.run_test() as pilot:
            await pilot.press("f8")
            pane = app._active_pane()
            assert pane.is_recording

            await pilot.press("ctrl+l")
            assert pane.is_live is False

            # Direct switch interaction (simulating a mouse click) is also blocked.
            pane.switch.value = True
            await pilot.pause()
            assert pane.switch.value is False
            assert pane.is_live is False


@pytest.mark.anyio
async def test_startup_without_file_prompts_for_filename(tmp_path):
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.TranscriptionWorker", side_effect=_FakeWorker):
        app = LiveApp(file=None, engine="faster-whisper", language="de")
        async with app.run_test() as pilot:
            assert isinstance(app.screen, NewFileModal)

            named = str(tmp_path / "first.md")
            await pilot.press(*list(named))
            await pilot.press("enter")
            await _wait_until(lambda: len(app._panes) == 1)

            assert app._active_pane().file == tmp_path / "first.md"


@pytest.mark.anyio
async def test_live_delta_leaves_marker_mid_stream_and_removes_it_on_stop(tmp_path):
    """Live mode splices confirmed deltas into the same anchor-tracked
    buffer as push-to-talk, leaving a fresh marker behind after each delta
    and removing it for good only on the final flush."""
    doc = tmp_path / "live.md"
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.LiveTranscriber", _FakeLiveTranscriber), \
         patch("resona_cli.live_ui.RecordingSession", side_effect=_FakeSession):
        app = LiveApp(file=doc, engine="faster-whisper", language="de")
        async with app.run_test() as pilot:
            await _wait_until(lambda: app.worker is not None)

            pane = app._active_pane()
            pane.switch.value = True
            await pilot.pause()
            assert pane.is_live

            await pilot.press("f8")
            assert pane.is_recording
            assert PLACEHOLDER_TEXT in pane.text_area.text

            pane.live_transcriber.push_delta("hallo welt")
            await _wait_until(lambda: "hallo welt" in pane.text_area.text)
            assert pane.text_area.text == f"hallo welt {PLACEHOLDER_TEXT}"

            live_segment_id = pane.live_segment_id
            segment = pane.segments[live_segment_id]
            assert segment.kind == "live"
            assert segment.status == "recording"

            pane.live_transcriber.set_flush_result("")
            await pilot.press("f8")
            await _wait_until(lambda: not pane.is_recording)

            assert pane.text_area.text == "hallo welt "
            assert PLACEHOLDER_TEXT not in pane.text_area.text
            manifest = json.loads(pane.manifest_path.read_text())
            entry = next(s for s in manifest["segments"] if s["id"] == live_segment_id)
            assert entry["kind"] == "live"
            assert entry["status"] == "inserted"


@pytest.mark.anyio
async def test_no_logs_tab_by_default(app):
    async with app.run_test() as pilot:
        tabs = app.query_one("#documents")
        with pytest.raises(Exception):
            tabs.get_pane("logs_tab")
        # log_msg is a no-op without --debug, even if called.
        app.log_msg("should be dropped")


@pytest.mark.anyio
async def test_debug_shows_logs_tab_and_captures_log_output(tmp_path):
    import logging

    doc = tmp_path / "notes.md"
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.TranscriptionWorker", side_effect=_FakeWorker):
        app = LiveApp(file=doc, engine="faster-whisper", language="de", debug=True)
        async with app.run_test() as pilot:
            tabs = app.query_one("#documents")
            assert tabs.get_pane("logs_tab") is not None

            # Log from a real background thread: the handler routes through
            # call_from_thread, which only works cross-thread (matching a
            # real engine/transcription worker thread logging, not the UI's
            # own event-loop thread).
            threading.Thread(
                target=lambda: logging.getLogger("resona_cli.test").info("hello from a test logger"),
            ).start()
            await asyncio.sleep(0.2)

            # RichLog defers rendering writes until its size is known, which
            # only happens once its tab is actually the visible one — switch
            # to it to flush the deferred lines.
            tabs.active = "logs_tab"
            await pilot.pause()

            log_display = app.query_one("#log_display", RichLog)
            log_text = "\n".join(line.text for line in log_display.lines)
            assert "hello from a test logger" in log_text


@pytest.mark.anyio
async def test_debug_logs_tab_stays_last_when_a_new_document_is_opened(tmp_path):
    doc = tmp_path / "notes.md"
    with patch("resona_cli.engine.InProcessEngine"), \
         patch("resona_cli.live_ui.TranscriptionWorker", side_effect=_FakeWorker):
        app = LiveApp(file=doc, engine="faster-whisper", language="de", debug=True)
        async with app.run_test() as pilot:
            await app._open_document(tmp_path / "second.md")

            # TabPane DOM/mount order doesn't reflect the visual tab-bar
            # order (ContentSwitcher.mount() always appends); check the
            # actual tab headers instead, stripping ContentTab's prefix.
            tabs = app.query_one("#documents")
            tab_ids = [t.id.removeprefix("--content-tab-") for t in tabs.query(Tab)]
            assert tab_ids[-1] == "logs_tab"
            assert tab_ids.count("logs_tab") == 1
            # The Logs tab is not tracked as a document.
            assert "logs_tab" not in app._panes
